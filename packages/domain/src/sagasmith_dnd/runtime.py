"""Compatibility entry point; implementation lives in Runtime.bootstrap."""


# Compatibility only: new application code imports Runtime directly.
_RUNTIME_EXPORTS = (
    'database',
    'dense_components',
)

def __getattr__(name: str):
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(name)
    from sagasmith_dnd._runtime_compat import runtime_attribute

    return runtime_attribute('bootstrap', name)
