"""Lazy legacy application exports, excluded from the domain dependency graph."""

from importlib import import_module


def runtime_attribute(module: str, name: str):
    try:
        implementation = import_module(f"sagasmith_dnd_runtime.{module}")
    except ModuleNotFoundError as exc:
        if exc.name != "sagasmith_dnd_runtime":
            raise
        raise RuntimeError(
            "This application entry point moved to sagasmith-dnd-runtime; "
            "install it with `pip install sagasmith-dnd-runtime`."
        ) from exc
    return getattr(implementation, name)
