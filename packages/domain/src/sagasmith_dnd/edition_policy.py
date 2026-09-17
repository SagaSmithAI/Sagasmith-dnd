"""Pure, explicitly selected policies for supported core rule editions."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .editions import normalize_dnd_edition
from .rule_registry import RuleRegistration, RuleRegistry


@dataclass(frozen=True)
class EditionPolicy:
    edition: str
    one_slot_per_turn: bool
    bonus_action_spell_limit: bool
    exhaustion_halves_max_hp: bool

    def validate_spell_turn(
        self, casts: list[dict[str, Any]], *, payment: str, spell_level: int,
        casting_time: str, spent_slot: bool,
    ) -> None:
        if self.one_slot_per_turn and spent_slot and any(item.get("spent_slot") for item in casts):
            raise ValueError("2024 rules allow only one expended spell slot per turn")
        if self.bonus_action_spell_limit and (
            payment == "bonus_action"
            or any(item.get("payment") == "bonus_action" for item in casts)
        ):
            candidates = [*casts, {"payment": payment, "spell_level": spell_level,
                                   "casting_time": casting_time}]
            if any(item.get("payment") != "bonus_action" and not (
                int(item.get("spell_level", 1)) == 0
                and str(item.get("casting_time") or "").startswith("1 action")
            ) for item in candidates):
                raise ValueError(
                    "2014 bonus-action spell rule permits only a 1-action cantrip "
                    "as another spell on the same turn"
                )

    def hit_point_maximum(self, base: int, exhaustion: int) -> int:
        return max(1, base // 2) if self.exhaustion_halves_max_hp and exhaustion >= 4 else base


CORE_POLICY_REGISTRY = RuleRegistry(tuple(
    RuleRegistration(
        id="dnd5e.core.edition-policy", kind="native", event="core.policy",
        source=f"dnd5e.core.{edition}", editions=(edition,),
        definition={"edition": edition, "one_slot_per_turn": edition == "2024",
                    "bonus_action_spell_limit": edition == "2014",
                    "exhaustion_halves_max_hp": edition == "2014"},
    ) for edition in ("2014", "2024")
))
EDITION_POLICIES = MappingProxyType({
    edition: EditionPolicy(**CORE_POLICY_REGISTRY.select(edition)[0].definition)
    for edition in ("2014", "2024")
})


def edition_policy(edition: str | None) -> EditionPolicy:
    return EDITION_POLICIES[normalize_dnd_edition(edition)]
