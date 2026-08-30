from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
UI_COMMAND_DOCUMENTS = (
    REPOSITORY_ROOT / "AGENTS.md",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "apps" / "ui" / "AGENTS.md",
    REPOSITORY_ROOT / "apps" / "ui" / "README.md",
)


def test_ui_clean_install_docs_use_the_workspace_lockfile() -> None:
    assert (REPOSITORY_ROOT / "package-lock.json").is_file()
    assert not (REPOSITORY_ROOT / "apps" / "ui" / "package-lock.json").exists()

    for path in UI_COMMAND_DOCUMENTS:
        content = path.read_text(encoding="utf-8")
        assert "npm ci" in content
        assert "npm --prefix apps/ui ci" not in content
