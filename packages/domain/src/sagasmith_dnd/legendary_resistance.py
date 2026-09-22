"""Source-bound 2014 Legendary Resistance and the failed-save decision boundary."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

MECHANIC = "dnd5e.core.save.legendary_resistance"
_HANDLER = ContextVar("dnd_failed_save_handler", default=None)


def source_contract(name, description):
    marker = re.fullmatch(r"Legendary Resistance \(([1-9]\d*)/Day\)", name)
    text = " ".join(description.split())
    if not marker or not re.fullmatch(
        r"If the [A-Za-z][A-Za-z '\-]* fails a saving throw, "
        r"it can choose to succeed instead\.", text,
    ):
        return None
    return {"kind": "legendary_resistance_2014", "maximum": int(marker[1]),
            "source_excerpt": text, "trigger": "failed_saving_throw"}


def feature(sheet):
    if sheet.get("edition") != "2014":
        return None
    for entry in sheet.get("content", {}).get("features", []):
        spec = entry.get("choices", {}).get("legendary_resistance")
        expected = source_contract(entry.get("name", ""), entry.get("description", ""))
        uses = entry.get("uses") or {}
        if (expected and spec == expected and MECHANIC in entry.get("mechanic_refs", [])
                and entry.get("source_key") and entry.get("rule_refs")
                and uses.get("max") == expected["maximum"]
                and uses.get("source_key") == entry["source_key"]
                and uses.get("recovers_on") == "long_rest"
                and not uses.get("unlimited")):
            return entry
    return None


def is_failed_save(result, sheet):
    if result.get("kind") == "save":
        return result.get("success") is False
    if result.get("kind") == "death_save":
        return result["failures"] > sheet.get("combat", {}).get("death_saves", {}).get(
            "failures", 0,
        )
    return False


def succeed(result, sheet):
    """Replace the failure, retaining every die and modifier in its receipt."""
    value = deepcopy(result)
    value["legendary_resistance"] = {"original_result": deepcopy(result),
                                     "mechanic_id": MECHANIC}
    value["success"] = True
    if value.get("automatic_failure"):
        value["automatic_failure"] = False
        value["reason"] = "legendary_resistance"
    if result.get("kind") == "death_save":
        from .breathing import breathing_blocks_recovery

        death = sheet.get("combat", {}).get("death_saves", {})
        successes = int(death.get("successes", 0)) + 1
        value.update(successes=min(3, successes), failures=int(death.get("failures", 0)),
                     outcome=("stable" if successes >= 3 and not breathing_blocks_recovery(sheet)
                              else "pending"))
    return value


class SaveDecisionRequiredError(Exception):
    def __init__(self, actor_id, sheet, result):
        self.actor_id = actor_id
        self.sheet = deepcopy(sheet)
        self.result = deepcopy(result)
        super().__init__("Legendary Resistance requires its owner's decision")


def settle_save(actor_id, sheet, result):
    entry = feature(sheet)
    if not entry or not is_failed_save(result, sheet):
        return result
    handler = _HANDLER.get()
    if handler is not None:
        return handler(actor_id, sheet, result, entry)
    if int(entry["uses"]["value"]) > 0:
        raise SaveDecisionRequiredError(actor_id, sheet, result)
    return result


@contextmanager
def saving_throw_decisions(handler):
    token = _HANDLER.set(handler)
    try:
        yield
    finally:
        _HANDLER.reset(token)
