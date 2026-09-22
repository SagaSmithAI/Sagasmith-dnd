"""Old application names delegate to the single Runtime implementation."""

import ast
import importlib
from importlib.metadata import distribution
from pathlib import Path

import pytest
import sagasmith_dnd
import sagasmith_dnd_runtime


@pytest.mark.parametrize("legacy,owner,name", [
    ("cli", "cli", "main"),
    ("runtime", "bootstrap", "database"),
    ("runtime", "bootstrap", "dense_components"),
    ("public_library", "public_library", "build_public_library"),
    ("content_packages", "content_assets", "attach_source_originals"),
    ("content_packages", "content_assets", "attach_auxiliary_assets"),
    ("content_packages", "content_assets", "attach_actor_portraits"),
    ("official_expansions", "official_library", "verify_official_expansion_library"),
    ("portrait_extraction", "portrait_extraction", "PortraitExtractor"),
])
def test_legacy_export_is_the_runtime_implementation(legacy, owner, name) -> None:
    old = importlib.import_module(f"sagasmith_dnd.{legacy}")
    current = importlib.import_module(f"sagasmith_dnd_runtime.{owner}")
    assert getattr(old, name) is getattr(current, name)


def test_console_script_is_owned_only_by_runtime() -> None:
    domain = distribution("sagasmith-dnd")
    runtime = distribution("sagasmith-dnd-runtime")
    assert not any(entry.name == "sagasmith-dnd" for entry in domain.entry_points)
    entries = [entry for entry in runtime.entry_points if entry.name == "sagasmith-dnd"]
    assert len(entries) == 1
    assert entries[0].value == "sagasmith_dnd_runtime.cli:main"


def test_runtime_declares_its_validation_dependency_without_mcp() -> None:
    requirements = distribution("sagasmith-dnd-runtime").requires or []
    assert any(item.startswith("pydantic") for item in requirements)
    assert not any(item.startswith("sagasmith-dnd-mcp") for item in requirements)


def test_runtime_imports_canonical_implementations_instead_of_legacy_exports() -> None:
    legacy = {}
    for path in Path(sagasmith_dnd.__file__).parent.glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_RUNTIME_EXPORTS"
                for target in node.targets
            ):
                legacy[f"sagasmith_dnd.{path.stem}"] = set(ast.literal_eval(node.value))
    for path in Path(sagasmith_dnd_runtime.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module in legacy:
                assert not any(
                    alias.name in legacy[node.module] or alias.name == "*"
                    for alias in node.names
                ), path
