"""Shared D&D runtime vocabularies with one semantic owner."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

ATTACK_MODES = frozenset({"melee", "ranged"})
ADVANCEMENT_MODES = frozenset({"milestone", "xp"})
CAMPAIGN_GAME_PHASES = frozenset({"lobby", "play"})
EFFECTIVE_GAME_PHASES = CAMPAIGN_GAME_PHASES | {"combat"}
COMBAT_OUTCOME_STATUSES = frozenset(
    {"defeat", "interrupted", "surrender", "truce", "victory", "withdrawal"}
)
REST_TYPES = frozenset({"long_rest", "short_rest"})
WEAPON_HAND_SLOTS = frozenset({"main_hand", "off_hand"})

DAMAGE_TYPES = frozenset(
    {
        "acid",
        "bludgeoning",
        "cold",
        "fire",
        "force",
        "lightning",
        "necrotic",
        "piercing",
        "poison",
        "psychic",
        "radiant",
        "slashing",
        "thunder",
    }
)

DENOMINATION_CP_VALUES: Mapping[str, int] = MappingProxyType(
    {"cp": 1, "sp": 10, "ep": 50, "gp": 100, "pp": 1000}
)
DENOMINATIONS = tuple(DENOMINATION_CP_VALUES)

GAMEPLAY_VISIBILITY_SCOPES = frozenset({"dm", "party", "public"})
PLAYER_GAMEPLAY_VISIBILITY_SCOPES = frozenset({"party", "public"})

PREPARATION_MODES = frozenset({"none", "known", "prepared", "spellbook"})
PREPARED_SELECTION_MODES = frozenset({"prepared", "spellbook"})
SPELLCASTING_RESOURCE_MODELS = PREPARATION_MODES - {"none"}

INVENTORY_OWNER_SCOPES = frozenset({"party", "character"})
