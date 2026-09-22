# Selected 2014 Fighting Styles

The bundled Fighter, Paladin and Ranger feature cards persist the chosen style
in `choices.option`. Runtime uses the source-bound feature ID and the 2014
edition. A style name on an unrelated card does not grant these mechanics.
Champion's Additional Fighting Style uses the same non-stacking selection rules.

| Style | Automatic effect or explicit choice |
| --- | --- |
| Archery | +2 to ranged **weapon** attacks, once in both the derived card and attack settlement. A thrown melee weapon remains a melee weapon for this prerequisite. Spell attacks do not qualify. |
| Defense | +1 AC while wearing armor; a shield alone does not qualify. |
| Dueling | +2 melee damage when wielding one weapon in one hand and no other weapons. A shield is allowed. |
| Two-Weapon Fighting | Retains the ability modifier on the qualifying offhand attack. The canonical Fighter/Ranger `choices.option` is supported alongside existing legacy cards. |
| Great Weapon Fighting | Opt in using `use_great_weapon_fighting: true`. A two-handed melee attack with a two-handed or versatile weapon rerolls each weapon die showing 1/2 once and keeps its replacement, including another 1/2. |
| Protection | Offers a reaction before the attack roll to an available shield bearer who can see the attacker and is within 5 feet of the other target. |

Great Weapon Fighting uses the declared `weapon_grip`, defaulting a two-handed
weapon to two hands. The preflight receipt exposes whether the style is
available. Opting in declares a policy for all qualifying dice in this attack;
omitting it declines rerolls. Critical weapon dice qualify. Sneak Attack and
separate additional damage dice do not. Dice are resolved left to right, with
each replacement drawn immediately after its original die. The receipt records
both values. Ordinary combat, reaction attacks, source plans and noncombat
source-object attacks share this behavior. For objects, supply `weapon_grip`
and `use_great_weapon_fighting` in `character_action`'s `attack_source_object`
payload.

## Protection in local play

1. Declare the attack normally. When eligible protectors exist, Runtime returns
   `pending_reaction` without rolling or spending the attack's action/ammunition.
2. Give every Protection window to its owning player. Use
   `combat_choice(action="resolve")` with the window ID and selection
   `{"id":"protection"}` or `{"id":"decline"}`. Acceptance revalidates the
   source, shield, geometry, visibility and reaction, then spends the reaction
   atomically. The attacker cannot choose for a different actor.
3. After all choices finish, use the returned `resume_attack.tool` and
   `resume_attack.arguments` with a **new** operation ID. For an ordinary attack,
   this repeats the exact declaration. An opportunity/Ready attack repeats its original release
   operation; a released Ready spell continues its stored spell resolution using
   `combat_resolve_attack`. For a paid source plan, resume the original commitment. Runtime
   applies accepted disadvantage only to this attack, without another window
   or reaction charge. Normal advantage/disadvantage cancellation still applies.

The local Host owns revision and branch metadata. An unknown or failed request
is retried with its **original** operation ID; only a known continuation uses
a new ID. Original declaration retries return the original window receipt,
even after the attack has subsequently finished. The durable declaration cannot
be replaced with another target, weapon, grip, source plan step or scene context.
The attacking actor may cancel its `protection_resume` window after all offered
choices finish; reactions already used are not refunded.

Grid mode uses full creature footprints and recorded visibility. Agent mode
requires `action.context.protection` when available shield bearers exist:

```json
{
  "decision_id": "shield-bearer-scene-facts",
  "reason": "The shield bearer is next to the target and can see the attacker.",
  "actors": [
    {"actor_id": "protector-id", "within_5_ft": true, "can_see_attacker": true}
  ]
}
```

Include exactly one entry for every available shield bearer whose target would
be another actor, including false facts for one too far away or unable to see.
These are DM scene facts, not computed modifiers or consent. Runtime derives
the source/equipment/reaction prerequisites from current cards and never invents
coordinates. Missing facts return a no-write ruling before RNG. Private intents
and other actors' choices stay out of player and party-public combat state.

Protection also pauses spell attacks, including a released Ready spell. A
readied spell may already have paid its casting cost; its stored resolution is
retained while the pre-roll choice is pending. Nested opportunity reactions keep
movement suspended until the protected attack and its later defenses finish.

Source: bundled SRD 2014 class Fighting Style sections in
`Fighter.md`, `Paladin.md`, and `Ranger.md`. These mechanics do not apply 2024
Fighting Style feat wording to a 2014 feature, or vice versa.
