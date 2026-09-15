"""Explicit registration of reviewed native execution hooks."""

from functools import lru_cache
from types import MappingProxyType


@lru_cache(maxsize=1)
def registry():
    from .native_content import activities, dependents, equipment

    registrations = (
        ("equipment.apply", equipment.apply_official_item_effect_to_encounter),
        ("activity.apply", activities.settle_core_activity_effect),
        ("dependent.validate", dependents._dependent_turn_contract),
        ("dependent.control", dependents._controlled_dependent),
        ("dependent.begin_turn", dependents._begin_dependent_turn),
    )
    result = {}
    for key, handler in registrations:
        if key in result:
            raise ValueError(f"duplicate native hook {key}")
        result[key] = handler
    return MappingProxyType(result)


def dispatch(hook, *args, **kwargs):
    try:
        handler = registry()[hook]
    except KeyError as error:
        raise ValueError(f"unregistered native hook {hook}") from error
    return handler(*args, **kwargs)
