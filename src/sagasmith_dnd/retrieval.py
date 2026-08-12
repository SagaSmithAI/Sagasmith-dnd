"""D&D-owned vocabulary for multilingual rule and module retrieval."""

from __future__ import annotations

from collections.abc import Sequence

DND5E_QUERY_HINTS: dict[str, Sequence[str]] = {
    "豁免": ("save", "saving"),
    "检定": ("check", "roll", "test"),
    "属性": ("ability", "score", "stat"),
    "技能": ("skill",),
    "熟练": ("proficient", "proficiency"),
    "攻击": ("attack", "strike"),
    "伤害": ("damage", "wound", "hurt"),
    "防御": ("defense", "armor", "protect"),
    "治疗": ("heal", "healing", "cure"),
    "法术": ("spell", "magic"),
    "武器": ("weapon", "arms"),
    "护甲": ("armor", "armour"),
    "骰子": ("dice", "roll"),
    "等级": ("level",),
    "经验": ("experience", "xp"),
    "线索": ("clue", "hint", "evidence"),
    "战斗": ("combat", "battle", "fight"),
    "营地": ("camp", "rest"),
    "物品": ("item", "object", "thing"),
    "门": ("door", "gate", "entrance"),
    "钥匙": ("key",),
    "宝藏": ("treasure", "loot"),
    "陷阱": ("trap", "hazard"),
    "怪物": ("monster", "creature", "beast"),
    "头目": ("boss", "leader", "chief"),
    "任务": ("quest", "mission", "task"),
    "奖励": ("reward", "prize"),
    "回合": ("turn", "round"),
    "移动": ("move", "movement"),
    "搜索": ("search", "explore", "scan"),
    "隐藏": ("hidden", "secret", "conceal"),
}

__all__ = ["DND5E_QUERY_HINTS"]
