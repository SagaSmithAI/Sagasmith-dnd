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
from .diseases import DiseasesService
from .downtime import DowntimeService
from .inventory import InventoryService
from .poisons import PoisonsService
from .presentation import PresentationService
from .shared import SharedService
from .spells import SpellsService
from .traps import TrapService


class ApplicationServices(
    TrapService,
    AttacksService,
    AuthoringService,
    CampaignsService,
    CharactersService,
    DiseasesService,
    DowntimeService,
    CombatService,
    ContentService,
    ContinuityService,
    InventoryService,
    PresentationService,
    PoisonsService,
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
