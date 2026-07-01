"""D&D service construction and optional dense retrieval."""

from __future__ import annotations

import os

from sagasmith_core import BgeEmbedder, Database, VectorStore, create_embedder


def database() -> Database:
    value = Database()
    value.upgrade_schema()
    return value


def dense_components() -> tuple[BgeEmbedder | None, VectorStore | None]:
    if os.environ.get("DND5E_DENSE_ENABLED", "0") != "1":
        return None, None
    return (
        create_embedder(env_prefix="DND5E"),
        VectorStore("dnd5e"),
    )

