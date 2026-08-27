from __future__ import annotations

import builtins
import os
import re
import subprocess
import sys
import textwrap
import tomllib
from importlib.metadata import metadata
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HEAVY_DISTRIBUTIONS = {
    "chromadb",
    "onnxruntime",
    "opencv-python",
    "pillow",
    "pymupdf",
    "pypdf",
    "pypdfium2",
    "rapidocr",
    "sentence-transformers",
    "torch",
}


def _project(path: str) -> dict:
    with (REPOSITORY_ROOT / path).open("rb") as source:
        return tomllib.load(source)["project"]


def _distribution_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    assert match is not None
    return match.group(0).casefold().replace("_", "-")


def _normalized(requirements: list[str]) -> set[str]:
    return {requirement.replace(" ", "").casefold() for requirement in requirements}


def test_base_wheels_do_not_require_heavy_local_kit_dependencies() -> None:
    for path in ("packages/domain/pyproject.toml", "packages/mcp/pyproject.toml"):
        dependencies = _project(path)["dependencies"]
        assert not ({_distribution_name(item) for item in dependencies} & HEAVY_DISTRIBUTIONS)
        core_requirements = [
            item for item in dependencies if _distribution_name(item) == "sagasmith-core"
        ]
        assert all("[" not in item.partition(">=")[0] for item in core_requirements)

    for distribution in ("sagasmith-dnd", "sagasmith-dnd-mcp"):
        installed = metadata(distribution).get_all("Requires-Dist") or []
        base_requirements = [item for item in installed if "extra ==" not in item]
        assert not (
            {_distribution_name(item) for item in base_requirements} & HEAVY_DISTRIBUTIONS
        )
        assert all(
            "[" not in item.partition(">=")[0]
            for item in base_requirements
            if _distribution_name(item) == "sagasmith-core"
        )


def test_optional_profiles_follow_the_runtime_import_boundaries() -> None:
    domain = _project("packages/domain/pyproject.toml")["optional-dependencies"]
    mcp = _project("packages/mcp/pyproject.toml")["optional-dependencies"]

    assert {"documents", "images", "embedding", "vector", "dense", "all"} <= set(domain)
    assert {"documents", "images", "ocr", "embedding", "vector", "dense", "all"} <= set(
        mcp
    )
    assert any("sagasmith-core[documents]" in item for item in _normalized(domain["documents"]))
    assert any("sagasmith-core[embedding]" in item for item in _normalized(domain["embedding"]))
    assert any("sagasmith-core[vector]" in item for item in _normalized(domain["vector"]))
    assert any("sagasmith-core[documents,ocr]" in item for item in _normalized(mcp["ocr"]))
    assert any("sagasmith-dnd[images]" in item for item in _normalized(mcp["images"]))


def test_minimal_text_mcp_starts_without_importing_heavy_capabilities(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import importlib.abc
        import os
        import sys
        from pathlib import Path

        blocked = {
            "PIL", "chromadb", "cv2", "fitz", "onnxruntime", "pymupdf", "pypdf",
            "pypdfium2", "rapidocr", "sentence_transformers", "torch",
        }

        class HeavyImportBlocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition(".")[0] in blocked:
                    raise ModuleNotFoundError(
                        f"blocked optional dependency: {fullname}", name=fullname
                    )
                return None

        sys.meta_path.insert(0, HeavyImportBlocker())
        home = Path(os.environ["SAGASMITH_MINIMAL_SMOKE_HOME"])
        os.environ["SAGASMITH_DND_MCP_HOME"] = str(home)
        os.environ["SAGASMITH_DND_MCP_AUTO_SEED"] = "0"
        os.environ["SAGASMITH_DND_MCP_RULE_OCR"] = "0"
        os.environ["SAGASMITH_DND_MCP_MODULE_OCR"] = "0"
        os.environ.pop("CHROMA_DB_PATH", None)
        os.environ.pop("CHROMA_DB_URL", None)

        from sagasmith_dnd_mcp.server import _render_combat_png, create_server
        from sagasmith_dnd.content_packages import _portrait_extractor_type
        from sagasmith_core import render_pdf_page

        assert create_server() is not None

        try:
            _render_combat_png({}, portraits={}, audience_projection="caller")
        except RuntimeError as exc:
            assert "sagasmith-dnd-mcp[images]" in str(exc)
        else:
            raise AssertionError("combat image rendering did not require the images extra")

        try:
            _portrait_extractor_type()
        except RuntimeError as exc:
            assert "sagasmith-dnd[images]" in str(exc)
        else:
            raise AssertionError("portrait extraction did not require the images extra")

        pdf = home / "missing-documents-extra.pdf"
        pdf.write_bytes(b"%PDF-1.4\\n")
        try:
            render_pdf_page(pdf, 1)
        except RuntimeError as exc:
            assert "sagasmith-core[documents]" in str(exc)
        else:
            raise AssertionError("PDF rendering did not require the documents extra")
        """
    )
    environment = os.environ.copy()
    environment["SAGASMITH_MINIMAL_SMOKE_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_optional_image_loaders_do_not_mask_unrelated_import_regressions(monkeypatch) -> None:
    from sagasmith_dnd.content_packages import _portrait_extractor_type

    from sagasmith_dnd_mcp.server import _render_combat_png

    real_import = builtins.__import__

    def fail_optional_module(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {
            "sagasmith_dnd.portrait_extraction",
            "sagasmith_dnd_mcp.combat_render",
        }:
            raise ModuleNotFoundError(
                "simulated internal dependency regression",
                name="unexpected_internal_dependency",
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_optional_module)

    with pytest.raises(ModuleNotFoundError, match="internal dependency regression"):
        _portrait_extractor_type()
    with pytest.raises(ModuleNotFoundError, match="internal dependency regression"):
        _render_combat_png({}, portraits={}, audience_projection="caller")
