"""Compatibility alias for the Runtime-owned implementation."""
import sys

from sagasmith_dnd_runtime import config as _implementation

sys.modules[__name__] = _implementation
