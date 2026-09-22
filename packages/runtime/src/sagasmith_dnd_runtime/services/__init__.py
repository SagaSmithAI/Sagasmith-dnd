"""Composition of application services; no protocol or dynamic source execution."""

import inspect
from functools import wraps

from .attacks import AttacksService
from .authoring import AuthoringService
from .campaigns import CampaignsService
from .characters import CharactersService
from .combat import CombatService
from .content import ContentService
from .continuity import ContinuityService
from .inventory import InventoryService
from .presentation import PresentationService
from .shared import SharedService
from .spells import SpellsService


class ApplicationServices(
    AttacksService,
    AuthoringService,
    CampaignsService,
    CharactersService,
    CombatService,
    ContentService,
    ContinuityService,
    InventoryService,
    PresentationService,
    SharedService,
    SpellsService,
):
    def bind(self, implementation: str, public_name: str):
        method = getattr(self, implementation)
        if inspect.iscoroutinefunction(method):
            @wraps(method)
            async def bound(*args, **kwargs):
                return await method(*args, **kwargs)
        else:
            @wraps(method)
            def bound(*args, **kwargs):
                return method(*args, **kwargs)
        # Keep operation aliases per instance; never mutate a shared class method.
        bound.__name__ = public_name
        return bound
