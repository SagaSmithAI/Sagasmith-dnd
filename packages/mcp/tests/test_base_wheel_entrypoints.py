from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sagasmith_core
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HEAVY_MODULES = {
    "PIL",
    "chromadb",
    "cv2",
    "fitz",
    "onnxruntime",
    "pymupdf",
    "pypdf",
    "pypdfium2",
    "rapidocr",
    "sentence_transformers",
    "torch",
}


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 180) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def base_wheel_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("base-wheel-entrypoints")
    dist = root / "dist"
    environment = root / "venv"
    core_root = Path(sagasmith_core.__file__).resolve().parents[2]

    _run(["uv", "build", "--wheel", "--out-dir", str(dist), str(core_root)])
    _run(
        [
            "uv",
            "build",
            "--wheel",
            "--package",
            "sagasmith-dnd",
            "--out-dir",
            str(dist),
        ],
        cwd=REPOSITORY_ROOT,
    )
    _run(
        [
            "uv",
            "build",
            "--wheel",
            "--package",
            "sagasmith-dnd-mcp",
            "--out-dir",
            str(dist),
        ],
        cwd=REPOSITORY_ROOT,
    )
    _run(["uv", "venv", "--python", sys.executable, str(environment)])

    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 3
    _run(["uv", "pip", "install", "--python", str(python), *map(str, wheels)])

    probe = (
        "import importlib.util; "
        f"blocked = {sorted(HEAVY_MODULES)!r}; "
        "found = [name for name in blocked if importlib.util.find_spec(name) is not None]; "
        "assert not found, f'heavy modules leaked into base wheels: {found}'"
    )
    _run([str(python), "-c", probe])

    console = python.parent / (
        "sagasmith-dnd-mcp.exe" if os.name == "nt" else "sagasmith-dnd-mcp"
    )
    assert console.is_file()
    return python, console


@pytest.mark.parametrize("entrypoint", ["console", "module"])
def test_final_base_wheel_stdio_entrypoints_initialize(
    base_wheel_runtime: tuple[Path, Path],
    entrypoint: str,
    tmp_path: Path,
) -> None:
    python, console = base_wheel_runtime
    environment = os.environ.copy()
    environment.pop("CHROMA_DB_PATH", None)
    environment.pop("CHROMA_DB_URL", None)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "SAGASMITH_DND_MCP_HOME": str(tmp_path / entrypoint),
            "SAGASMITH_DND_MCP_AUTO_SEED": "0",
            "SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS": "",
            "SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS": "",
            "SAGASMITH_DND_MCP_RULE_OCR": "0",
            "SAGASMITH_DND_MCP_MODULE_OCR": "0",
            "SAGASMITH_DND_SKILLS_DIR": str(REPOSITORY_ROOT / "skills"),
        }
    )
    command = str(console) if entrypoint == "console" else str(python)
    arguments = [] if entrypoint == "console" else ["-m", "sagasmith_dnd_mcp.server"]
    params = StdioServerParameters(
        command=command,
        args=arguments,
        cwd=REPOSITORY_ROOT,
        env=environment,
    )

    async def initialize() -> None:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.server_info.name

    asyncio.run(asyncio.wait_for(initialize(), timeout=45))
