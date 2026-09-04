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


def held_item_roots(sheet: dict[str, Any]) -> list[str]:
    """Return stable, unique IDs of items actually held in either hand.

    Validation is deliberately performed on the complete sheet so stale slot
    references cannot be interpreted as a drop.  A shield is worn/strapped,
    not held for this rule, even if a future compatible schema represents it
    as armor with ``mechanics.category == "shield"``.
    """

    value = validate_character_sheet(sheet)
    items = {item["id"]: item for item in value["inventory"]["items"]}
    roots: list[str] = []
    for slot in ("main_hand", "off_hand"):
        item_id = value["inventory"]["equipment_slots"][slot]
        if item_id is None or item_id in roots:
            continue
        item = items[item_id]
        mechanics = dict(item.get("mechanics") or {})
        if item["kind"] == "shield" or (
            item["kind"] == "armor" and mechanics.get("category") == "shield"
        ):
            continue
        roots.append(item_id)
    return roots
