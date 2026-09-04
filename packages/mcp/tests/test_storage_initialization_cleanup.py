from pathlib import Path

import pytest

import sagasmith_dnd_mcp.storage as storage_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_partial_storage_initialization_disposes_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool
) -> None:
    events: list[str] = []
    original = RuntimeError("vector initialization failed")
    initial_cause = LookupError("vector configuration unavailable")
    cleanup_error = OSError("database cleanup diagnostic")
    real_database = storage_module.Database

    class OpenedDatabase(real_database):
        def __init__(self, url: str) -> None:
            super().__init__(url)
            # Force a real pooled SQLite handle before the later constructor fails.
            with self.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")

        def dispose(self) -> None:
            events.append("database-disposed")
            super().dispose()
            if cleanup_fails:
                raise cleanup_error

    def fail_vector_store(_system_id: str) -> None:
        raise original from initial_cause

    monkeypatch.setattr(storage_module, "Database", OpenedDatabase)
    monkeypatch.setattr(storage_module, "VectorStore", fail_vector_store)
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )
    with pytest.raises(RuntimeError) as caught:
        create_server(config)

    assert caught.value is original
    assert events == ["database-disposed"]
    if cleanup_fails:
        assert isinstance(original.__cause__, BaseExceptionGroup)
        assert original.__cause__.exceptions == (initial_cause, cleanup_error)
    else:
        assert original.__cause__ is initial_cause
    # On Windows this also verifies that a pooled handle no longer locks the file.
    released = tmp_path / "released.sqlite3"
    config.database_path.rename(released)
    assert released.is_file()
