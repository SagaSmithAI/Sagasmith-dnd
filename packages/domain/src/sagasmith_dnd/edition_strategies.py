"""Small edition strategies, grouped by rule family rather than Runtime operation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RestPolicy:
    edition: str

    def recover_exhaustion(self, exhaustion, *, food_and_drink):
        return max(0, exhaustion - 1) if self.edition == "2024" or food_and_drink else exhaustion

    def hit_die_healing(self, rolled_healing):
        return max(1 if self.edition == "2024" else 0, rolled_healing)

    def recover_hit_dice(self, hit_dice, hit_dice_recovery=None):
        if self.edition == "2024":
            allocation = {
                key: int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
                for key, resource in hit_dice.items()
                if isinstance(resource, dict)
            }
        else:
            missing = {
                key: int(resource.get("max", 0) or 0) - int(resource.get("value", 0) or 0)
                for key, resource in hit_dice.items()
                if isinstance(resource, dict)
            }
            allowance = max(
                1,
                sum(
                    int(resource.get("max", 0) or 0)
                    for resource in hit_dice.values()
                    if isinstance(resource, dict)
                )
                // 2,
            )
            if hit_dice_recovery is None:
                if (
                    sum(1 for amount in missing.values() if amount > 0) > 1
                    and sum(missing.values()) > allowance
                ):
                    raise ValueError(
                        "2014 long-rest hit-die recovery needs a player allocation"
                    )
                allocation = {
                    key: min(amount, allowance) for key, amount in missing.items()
                }
            else:
                if not isinstance(hit_dice_recovery, dict):
                    raise ValueError(
                        "2014 hit-die recovery allocation must be an object"
                    )
                unknown_keys = set(hit_dice_recovery) - set(missing)
                if unknown_keys:
                    raise ValueError(
                        "2014 hit-die recovery allocation contains an unknown hit die"
                    )
                if any(
                    isinstance(amount, bool)
                    or not isinstance(amount, int)
                    or amount < 0
                    for amount in hit_dice_recovery.values()
                ):
                    raise ValueError(
                        "2014 hit-die recovery counts must be non-negative integers"
                    )
                allocation = {
                    key: hit_dice_recovery.get(key, 0) for key in missing
                }
            if (
                any(amount < 0 or amount > missing[key] for key, amount in allocation.items())
                or sum(allocation.values()) > allowance
            ):
                raise ValueError("2014 hit-die recovery allocation is invalid")
        return allocation


@dataclass(frozen=True)
class D20Policy:
    edition: str

    def exhaustion_adjustment(self, *, exhaustion, kind, bonus=0, disadvantage=False):
        if isinstance(exhaustion, bool) or not isinstance(exhaustion, int) or exhaustion < 0:
            raise ValueError("exhaustion must be a non-negative integer")
        if kind not in {"ability", "attack", "check", "death_save", "initiative", "save"}:
            raise ValueError("unsupported exhaustion roll kind")
        adjusted_bonus = int(bonus)
        adjusted_disadvantage = bool(disadvantage)
        exhaustion_disadvantage = False
        if self.edition == "2024":
            adjusted_bonus -= 2 * exhaustion
        elif (
            kind in {"ability", "check"} | {"initiative"}
            and exhaustion >= 1
            or kind in {"attack", "death_save", "save"}
            and exhaustion >= 3
        ):
            adjusted_disadvantage = True
            exhaustion_disadvantage = True
        return {
            "bonus": adjusted_bonus,
            "disadvantage": adjusted_disadvantage,
            "exhaustion_disadvantage": exhaustion_disadvantage,
            "applied": adjusted_bonus != int(bonus) or adjusted_disadvantage != bool(disadvantage),
        }
