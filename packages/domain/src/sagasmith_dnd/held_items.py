"""Selection of items physically held when a 2014 actor becomes Unconscious.

The 2014 Magic Items reference (``09_Magic_Items/Magic_Items.md``,
“Wearing and Wielding Items”) distinguishes a weapon that must be held from
magic armor and a shield strapped to the arm.  This module therefore selects
only the two hand slots; worn equipment remains worn.  It returns roots only:
container contents and other referenced items are resolved by the ground-drop
transaction rather than guessed here.
"""

from __future__ import annotations

from typing import Any

from .character_schema import validate_character_sheet


def is_attuned_custody_locked_item(item: dict[str, Any]) -> bool:
    """Whether an attuned reviewed item cannot leave its owner's custody."""

    contract = dict(item.get("mechanics", {}).get("official_item") or {})
    return (
        item.get("attunement") == "attuned"
        and (
            (
                contract.get("kind") == "armblade"
                and contract.get("inseparable_while_attuned") is True
            )
            or (
                contract.get("kind") == "dyrrn_tentacle_whip"
                and contract.get("cursed_attunement") is True
            )
        )
    )


def held_item_roots(sheet: dict[str, Any]) -> list[str]:
    """Return stable, unique IDs of items actually held in either hand.

    Validation is deliberately performed on the complete sheet so stale slot
    references cannot be interpreted as a drop.  A shield is worn/strapped,
    not held for this rule; the schema has a separate ``shield`` slot.  An
    attuned Armblade is physically inseparable from its owner and therefore is
    not a droppable root while its reviewed contract is active.  A cursed
    Dyrrn's Tentacle Whip is likewise kept within the owner's reach.
    """

    value = validate_character_sheet(sheet)
    roots: list[str] = []
    for slot in ("main_hand", "off_hand"):
        item_id = value["inventory"]["equipment_slots"][slot]
        if item_id is None or item_id in roots:
            continue
        item = next(item for item in value["inventory"]["items"] if item["id"] == item_id)
        if is_attuned_custody_locked_item(item):
            continue
        roots.append(item_id)
    return roots


__all__ = ["held_item_roots", "is_attuned_custody_locked_item"]
