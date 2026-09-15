"""Compatibility alias for the Runtime-owned implementation."""
import sys

from sagasmith_dnd_runtime import actor_memory as _implementation

sys.modules[__name__] = _implementation
