"""Reviewed 2014 Bardic Inspiration: grants and a post-d20 decision boundary."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from .legendary_resistance import SaveDecisionRequiredError

FEATURE = "dnd5e.content.srd2014.feature.bard-bardic-inspiration"
MECHANIC = "dnd5e.core.class.bardic_inspiration"
KIND = "bardic_inspiration_2014"
_HANDLER = ContextVar("dnd_inspiration_handler", default=None)


def feature(sheet):
    if sheet.get("edition") != "2014":
        return None
    return next(
        (
            f
            for f in sheet.get("content", {}).get("features", [])
            if f.get("id") == FEATURE
            and MECHANIC in f.get("mechanic_refs", [])
            and f.get("pack_id") == "dnd5e.content.srd2014"
            and f.get("rule_refs")
        ),
        None,
    )


def die_size(sheet):
    level = sum(
        int(c.get("level", 0))
        for c in sheet.get("progression", {}).get("classes", [])
        if c.get("name", "").casefold() == "bard"
    )
    if not feature(sheet) or not 1 <= level <= 20:
        raise ValueError(
            "Bardic Inspiration requires the reviewed 2014 Bard feature and class level"
        )
    return 12 if level >= 15 else 10 if level >= 10 else 8 if level >= 5 else 6


def held(sheet):
    if sheet.get("edition") != "2014":
        return None
    effects = [e for e in sheet.get("effects", []) if e.get("active") and e.get("kind") == KIND]
    if len(effects) > 1:
        raise ValueError("a creature cannot hold multiple Bardic Inspiration dice")
    if not effects:
        return None
    effect = effects[0]
    data = effect.get("metadata", {})
    if (
        data.get("mechanic_id") != MECHANIC
        or data.get("die_size") not in {6, 8, 10, 12}
        or not data.get("source_actor_id")
        or not data.get("rule_refs")
        or effect.get("source") != FEATURE
    ):
        raise ValueError("Bardic Inspiration effect has no valid source grant")
    return effect


def grant_effect(sheet, target, *, source_actor_id, effect_id):
    from .conditions import condition_ids

    if target.get("edition") != "2014":
        raise ValueError("Bardic Inspiration editions must match")
    if held(target):
        raise ValueError("target already holds a Bardic Inspiration die")
    if (
        condition_ids(sheet.get("conditions"))
        & {
            "incapacitated",
            "unconscious",
            "paralyzed",
            "petrified",
            "stunned",
            "dead",
        }
        or int(sheet.get("combat", {}).get("hp", {}).get("value", 0)) <= 0
    ):
        raise ValueError("Bard cannot grant inspiration under current conditions")
    if "deafened" in condition_ids(target.get("conditions")):
        raise ValueError("target cannot hear the Bard")
    entry = feature(sheet)
    sides = die_size(sheet)
    return {
        "id": effect_id,
        "name": "Bardic Inspiration",
        "kind": KIND,
        "source": FEATURE,
        "active": True,
        "concentration": False,
        "duration": {"period": "minute", "remaining": 10},
        "changes": [],
        "metadata": {
            "mechanic_id": MECHANIC,
            "die_size": sides,
            "source_actor_id": source_actor_id,
            "rule_refs": deepcopy(entry["rule_refs"]),
        },
    }


class InspirationDecisionRequiredError(SaveDecisionRequiredError):
    def __init__(self, actor_id, sheet, result):
        super().__init__(actor_id, sheet, result)
        self.args = ("Bardic Inspiration requires its owner's post-d20 decision",)


def settle_roll(actor_id, sheet, result, *, rng=None):
    effect = held(sheet)
    if (
        not effect
        or result.get("kind") not in {"ability", "attack", "save", "death_save", "initiative"}
        or type(result.get("natural")) is not int
        or not result.get("rolls")
    ):
        return result
    handler = _HANDLER.get()
    if handler is None:
        raise InspirationDecisionRequiredError(actor_id, sheet, result)
    return handler(actor_id, sheet, result, effect, rng)


def add_die(result, sheet, effect, *, rng=None):
    from .breathing import breathing_blocks_recovery
    from .engine import resolve_death_save
    from .random_stream import active_random_source

    generator = rng or active_random_source()
    if generator is None:
        raise ValueError("Bardic Inspiration requires an authoritative random source")
    amount = generator.randint(1, effect["metadata"]["die_size"])
    value = deepcopy(result)
    value["total"] += amount
    if result["kind"] in {"ability", "save"}:
        value["success"] = value["total"] >= value["dc"]
    elif result["kind"] == "attack":
        value["hit"] = result["natural"] == 20 or (
            result["natural"] != 1 and value["total"] >= value["armor_class"]
        )
    elif result["kind"] == "death_save":
        # Recalculate counters with the same selected natural value, retaining
        # natural 1/20 semantics and every original advantage/reroll receipt.
        class OriginalDie:
            def randint(self, low, high):
                return result["natural"]

        death = sheet.get("combat", {}).get("death_saves", {})
        recalculated = resolve_death_save(
            successes=int(death.get("successes", 0)),
            failures=int(death.get("failures", 0)),
            bonus=result["bonus"] + amount,
            rng=OriginalDie(),
            recovery_allowed=not breathing_blocks_recovery(sheet),
        )
        value.update({k: recalculated[k] for k in ("successes", "failures", "outcome")})
    value["bardic_inspiration"] = {
        "effect_id": effect["id"],
        "die_size": effect["metadata"]["die_size"],
        "rolled": amount,
        "original_total": result["total"],
        "source_actor_id": effect["metadata"]["source_actor_id"],
        "rule_refs": deepcopy(effect["metadata"]["rule_refs"]),
        "mechanic_id": MECHANIC,
    }
    return value


@contextmanager
def inspiration_decisions(handler):
    token = _HANDLER.set(handler)
    try:
        yield
    finally:
        _HANDLER.reset(token)
