"""Shared D&D runtime vocabularies with one semantic owner."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

ATTACK_MODES = frozenset({"melee", "ranged"})

DENOMINATION_CP_VALUES: Mapping[str, int] = MappingProxyType(
    {"cp": 1, "sp": 10, "ep": 50, "gp": 100, "pp": 1000}
)
DENOMINATIONS = tuple(DENOMINATION_CP_VALUES)

GAMEPLAY_VISIBILITY_SCOPES = frozenset({"dm", "party", "public"})
PLAYER_GAMEPLAY_VISIBILITY_SCOPES = frozenset({"party", "public"})

PREPARATION_MODES = frozenset({"none", "known", "prepared", "spellbook"})
PREPARED_SELECTION_MODES = frozenset({"prepared", "spellbook"})

INVENTORY_OWNER_SCOPES = frozenset({"party", "character"})
