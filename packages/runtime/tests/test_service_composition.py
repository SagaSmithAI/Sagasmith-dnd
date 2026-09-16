"""Guard the extraction boundary and per-instance state isolation."""

import ast
from pathlib import Path

from sagasmith_dnd_runtime import application
from sagasmith_dnd_runtime.config import McpConfig


def test_composer_has_no_domain_handlers_or_dynamic_source_execution() -> None:
    source = Path(application.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "_create_application")
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   for node in ast.walk(factory) if node is not factory)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"exec", "eval", "compile"}


def test_independent_service_instances_keep_registries_and_storage_isolated(tmp_path) -> None:
    runtimes = []
    try:
        for name in ("first", "second"):
            root = tmp_path / name
            runtimes.append(application.create_runtime(McpConfig(
                home=root, database_url=None, chroma_url=None, chroma_path_override=None,
                dnd_skills_dir=root / "skills", modulegen_skills_dir=root / "modulegen",
                auto_seed_rules=False,
            )))
        first, second = runtimes
        create = first.operations["campaign_create"].function
        campaign = create(name="Only first", edition="2014", idempotency_key="create")
        query = second.operations["campaign_query"].function
        assert campaign["id"] not in str(query(view="list"))
        assert first.operations is not second.operations
        assert first.contract() == second.contract()
        assert create.__module__.endswith("services.campaigns")
    finally:
        for runtime in runtimes:
            runtime.close()
