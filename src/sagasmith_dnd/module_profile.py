"""D&D-specific enrichment for the system-neutral module parser."""

from __future__ import annotations

import re

from sagasmith_core.modules import GenericModuleProfile

_ROOM = re.compile(r"^[A-Z]{1,3}\d+[A-Za-z]?\s*[.．]")
_STAT_SIGNALS = (
    "armor class",
    "hit points",
    "speed",
    "damage immunities",
    "condition immunities",
    "actions",
    "护甲等级",
    "生命值",
    "速度",
    "伤害免疫",
    "状态免疫",
    "动作",
)
_KEYWORDS = {
    "trap": ("trap", "陷阱"),
    "npc": ("npc", "非玩家角色"),
    "monster": ("monster", "怪物"),
    "reward": ("reward", "treasure", "奖励", "宝藏"),
    "encounter": ("encounter", "遭遇"),
    "clue": ("clue", "线索"),
}


class DndModuleProfile(GenericModuleProfile):
    name = "dnd5e"
    version = "1"

    def classify_chunk(self, heading: str, text: str) -> str:
        if _ROOM.match(heading):
            return "room"
        folded = text.casefold()
        if sum(signal in folded for signal in _STAT_SIGNALS) >= 2:
            return "statblock"
        if any(value in folded for value in _KEYWORDS["encounter"]):
            return "encounter"
        return super().classify_chunk(heading, text)

    def keywords(self, title: str, text: str) -> list[str]:
        values = super().keywords(title, text)
        folded = f"{title}\n{text}".casefold()
        for key, signals in _KEYWORDS.items():
            if any(signal in folded for signal in signals):
                values.append(key)
        return list(dict.fromkeys(values))
