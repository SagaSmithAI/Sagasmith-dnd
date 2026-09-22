"""Explicit physical casting supplies for spell settlement fixtures."""

from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet


def with_component_pouch(sheet=None):
    value = default_character_sheet() if sheet is None else sheet
    return add_inventory_item(value, {
        "id": "component-pouch", "name": "Component pouch", "kind": "equipment",
        "mechanics": {"spell_component": {
            "kind": "pouch", "source": "SRD 2014 Equipment: Component Pouch",
        }},
    })[0]
