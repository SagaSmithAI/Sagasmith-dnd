"""Rules and content contracts must remain usable without application packages."""

import ast
import subprocess
import sys
from pathlib import Path

import sagasmith_dnd


def test_domain_has_no_application_or_transport_imports() -> None:
    root = Path(sagasmith_dnd.__file__).parent
    forbidden = ("sagasmith_dnd_runtime", "sagasmith_dnd_mcp", "mcp", "fastapi", "PIL")
    application_symbols = {"Database", "VectorStore", "BgeEmbedder", "create_embedder"}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                if node.module == "sagasmith_core":
                    assert not any(
                        item.name in application_symbols or item.name.endswith("Service")
                        for item in node.names
                    ), path
            elif isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            else:
                continue
            assert not any(
                module == banned or module.startswith(banned + ".")
                for module in modules for banned in forbidden
            ), path


def test_domain_imports_without_runtime_and_reports_legacy_install_requirement() -> None:
    script = '''
import importlib
import importlib.abc
import pkgutil
import sys

class BlockApplications(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sagasmith_dnd_runtime', 'sagasmith_dnd_mcp'}:
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, BlockApplications())
import sagasmith_dnd
for item in pkgutil.walk_packages(sagasmith_dnd.__path__, sagasmith_dnd.__name__ + '.'):
    importlib.import_module(item.name)
from sagasmith_dnd.primitive_contracts import capability_manifest
assert capability_manifest()
from sagasmith_dnd import cli
try:
    cli.main
except RuntimeError as exc:
    assert 'pip install sagasmith-dnd-runtime' in str(exc)
else:
    raise AssertionError('Legacy application entry point must explain missing Runtime')
'''
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
