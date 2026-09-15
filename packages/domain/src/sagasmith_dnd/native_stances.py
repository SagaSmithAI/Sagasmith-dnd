"""Reviewed native stance registration; the combat kernel knows only these hooks."""

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def handlers() -> tuple[Any, ...]:
    from sagasmith_dnd.native_content import tortle

    return (tortle.HANDLER,)


def active(actor):
    return any(handler.active(actor) for handler in handlers())


def require_action_allowed(actor):
    for handler in handlers():
        handler.require_action(actor)


def reconcile(actor, sheet):
    for handler in handlers():
        handler.reconcile(actor, sheet)


def exit_actions(actor, budget):
    return [
        action
        for handler in handlers()
        if handler.active(actor)
        for action in handler.exit_actions
        if budget.get("bonus_action", 0) > 0
    ]


def extra_actions(actor):
    return [
        action
        for handler in handlers()
        if handler.available(actor)
        for action in handler.enter_actions
    ]


def supported_actions():
    return {action for handler in handlers() for action in handler.actions}


def is_exit_action(action):
    return any(action in handler.exit_actions for handler in handlers())


def apply_action(action, actor, flags, budget):
    for handler in handlers():
        if action in handler.actions:
            handler.apply(action, actor, flags, budget)
            return True
    return False


def save_modifiers(encounter, actor_id, *, ability):
    values = [handler.saves(encounter, actor_id, ability=ability) for handler in handlers()]
    return any(value[0] for value in values), any(value[1] for value in values)


def mechanic_ids():
    return [handler.mechanic_id for handler in handlers()]


def persistent_flags():
    return {handler.flag for handler in handlers()}


def start_turn(actor, flags, budget):
    for handler in handlers():
        handler.start_turn(actor, flags, budget)


def affects_dodge(action):
    return any(action in handler.enter_actions for handler in handlers())


def legacy_export(name):
    if name.startswith("__"):
        raise AttributeError(name)
    from sagasmith_dnd.native_content import tortle

    if hasattr(tortle, name):
        return getattr(tortle, name)
    raise AttributeError(name)
