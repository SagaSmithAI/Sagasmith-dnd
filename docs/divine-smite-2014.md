# 2014 Divine Smite

Select the bundled Paladin Divine Smite feature through `character_content_apply`.
A qualifying melee weapon hit with an available spell slot returns `pending_hit`
before damage. The owner chooses using the returned `character_state_change`
contract, action `divine_smite`, payload `{choice_id, accept: true, slot: "1"}`.
Use one exact offered slot key, including `pact_magic` where available. Decline
with `{choice_id, accept: false}`. No slot is selected automatically.

The recorded hit and immutable command survive restart. Resumption reuses the
original random prefix and commits the selected slot, damage, HP, attack payment
and receipts together. Stale, invalid and conflicting requests change no game
state. Unknown writes must replay their original operation ID and arguments.

Damage is 2d8 radiant at slot level 1, increasing to 5d8 at level 4 and capped
there for higher slots. A target whose authoritative species/type is exactly
undead or fiend (with optional subtype) adds one d8, for a maximum of 6d8.
Labels such as "fiendish human" do not imply a creature type. Critical hits double
these dice; per-part resistance, immunity and vulnerability remain authoritative.
The feature spends no extra action or reaction and is available on each qualifying
hit, including Extra Attack and reaction attacks. Misses, unarmed strikes, ranged
attacks and spell attacks do not offer it. It is not spellcasting.

This contract is edition-bound to 2014. Improved Divine Smite and 2024's changed
Smite spell/action model are separate features.
