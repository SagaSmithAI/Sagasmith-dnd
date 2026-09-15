"""Read-only adapters for the D&D and module-generation skill repositories."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

TEXT_ASSET_EXTENSIONS = {
    ".csv",
    ".json",
    ".md",
    ".rst",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_ASSET_DIRECTORIES = {
    "data",
    "reference",
    "references",
    "template",
    "templates",
}


@dataclass(frozen=True)
class SkillDocument:
    id: str
    title: str
    source: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class SkillAsset:
    id: str
    source: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class SkillSnapshot:
    documents: tuple[SkillDocument, ...]
    assets: tuple[SkillAsset, ...]
    contents: Mapping[str, bytes]
    dependencies: tuple[SkillAsset, ...]


class SkillCatalog:
    def __init__(self, *, dnd_root: Path, modulegen_root: Path) -> None:
        self._roots = {"dnd": dnd_root, "modulegen": modulegen_root}
        self._snapshot: SkillSnapshot | None = None
        self._snapshot_lock = RLock()

    def refresh(self) -> None:
        """Discard filesystem indexes before an explicit installation reload."""

        with self._snapshot_lock:
            self._snapshot = self._build_snapshot()

    def _build_snapshot(self) -> SkillSnapshot:
        documents = []
        assets = []
        contents = {}
        dependencies = []
        for source, configured_root in self._roots.items():
            root = configured_root.resolve()
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if self._is_install_shadow(path, root) or not path.is_file():
                    continue
                if not path.resolve().is_relative_to(root):
                    continue
                relative = path.relative_to(root)
                is_document = path.name == "SKILL.md"
                # Include the text package, not only specially named reference
                # folders: linked examples and README guidance affect behavior too.
                is_asset = path.suffix.lower() in TEXT_ASSET_EXTENSIONS and not is_document
                content = path.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                if is_document:
                    parent = relative.parent
                    suffix = "root" if parent == Path(".") else ".".join(parent.parts)
                    identifier = f"{source}.{suffix}"
                    title = next(
                        (
                            line[2:].strip()
                            for line in content.decode("utf-8").splitlines()
                            if line.startswith("# ")
                        ),
                        suffix,
                    )
                    documents.append(SkillDocument(identifier, title, source, path, checksum))
                    contents[identifier] = content
                if is_asset:
                    identifier = f"{source}:{relative.as_posix()}"
                    assets.append(SkillAsset(identifier, source, path, checksum))
                    contents[identifier] = content
                elif not is_document:
                    identifier = f"{source}:{relative.as_posix()}"
                    dependencies.append(SkillAsset(identifier, source, path, checksum))
                    contents[identifier] = content
        return SkillSnapshot(
            tuple(documents),
            tuple(assets),
            MappingProxyType(contents),
            tuple(dependencies),
        )

    def _current(self) -> SkillSnapshot:
        with self._snapshot_lock:
            if self._snapshot is None:
                self._snapshot = self._build_snapshot()
            return self._snapshot

    def root(self, source: str) -> Path:
        """Return one configured repository root without exposing mutation."""

        try:
            return self._roots[source]
        except KeyError as error:
            raise LookupError(f"unknown skill source {source!r}") from error

    def list(self) -> list[SkillDocument]:
        return list(self._current().documents)

    def get(self, skill_id: str) -> SkillDocument:
        for document in self._current().documents:
            if document.id == skill_id:
                return document
        raise LookupError(f"unknown skill document {skill_id!r}")

    def read(self, skill_id: str) -> str:
        snapshot = self._current()
        if not any(item.id == skill_id for item in snapshot.documents):
            raise LookupError(f"unknown skill document {skill_id!r}")
        return snapshot.contents[skill_id].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")

    def assets(self) -> list[SkillAsset]:
        return list(self._current().assets)

    def read_asset(self, asset_id: str) -> str:
        snapshot = self._current()
        if not any(item.id == asset_id for item in snapshot.assets):
            raise LookupError(f"unknown skill asset {asset_id!r}")
        return snapshot.contents[asset_id].decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")

    def get_asset(self, asset_id: str) -> SkillAsset:
        for asset in self._current().assets:
            if asset.id == asset_id:
                return asset
        raise LookupError(f"unknown skill asset {asset_id!r}")

    @staticmethod
    def resource_id(asset_id: str) -> str:
        """Encode a slash-containing asset id for a single MCP URI path segment."""
        return base64.urlsafe_b64encode(asset_id.encode("utf-8")).decode("ascii").rstrip("=")

    def read_resource_asset(self, resource_id: str) -> str:
        padding = "=" * (-len(resource_id) % 4)
        try:
            asset_id = base64.urlsafe_b64decode(resource_id + padding).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise LookupError(f"invalid skill asset resource id {resource_id!r}") from error
        return self.read_asset(asset_id)

    def outline(self, *, kind: str, identifier: str) -> dict[str, Any]:
        """Return a compact heading index without loading a whole skill document."""

        text = self._text(kind=kind, identifier=identifier)
        headings = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match is None:
                continue
            headings.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2),
                    "line": line_number,
                }
            )
        return {
            "kind": kind,
            "identifier": identifier,
            "bytes": len(text.encode("utf-8")),
            "approx_tokens": (len(text.encode("utf-8")) + 3) // 4,
            "headings": headings,
        }

    def section(
        self,
        *,
        kind: str,
        identifier: str,
        heading: str,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        """Read one Markdown section with an explicit bounded-output contract."""

        if max_chars < 256 or max_chars > 20_000:
            raise ValueError("max_chars must be between 256 and 20000")
        text = self._text(kind=kind, identifier=identifier)
        lines = text.splitlines()
        candidates: list[tuple[int, int, str]] = []
        wanted = heading.strip().casefold()
        for index, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match is None:
                continue
            title = match.group(2).strip()
            if title.casefold() == wanted:
                candidates = [(index, len(match.group(1)), title)]
                break
            if wanted and wanted in title.casefold():
                candidates.append((index, len(match.group(1)), title))
        if not candidates:
            raise LookupError(f"unknown heading {heading!r} in {kind} document {identifier!r}")
        if len(candidates) > 1:
            raise LookupError(
                f"ambiguous heading {heading!r}; matches: "
                + ", ".join(item[2] for item in candidates[:10])
            )
        start, level, title = candidates[0]
        end = len(lines)
        for index in range(start + 1, len(lines)):
            match = re.match(r"^(#{1,6})\s+", lines[index])
            if match is not None and len(match.group(1)) <= level:
                end = index
                break
        content = "\n".join(lines[start:end]).strip() + "\n"
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars].rstrip() + "\n"
        return {
            "kind": kind,
            "identifier": identifier,
            "heading": title,
            "line_start": start + 1,
            "line_end": end,
            "truncated": truncated,
            "content": content,
        }

    def search(
        self,
        *,
        kind: str,
        query: str,
        identifier: str | None = None,
        limit: int = 8,
        context_chars: int = 900,
    ) -> dict[str, Any]:
        """Search installed guidance and return bounded, line-addressed excerpts."""

        if not query.strip():
            raise ValueError("query is required")
        if limit < 1 or limit > 20:
            raise ValueError("limit must be between 1 and 20")
        if context_chars < 200 or context_chars > 2_000:
            raise ValueError("context_chars must be between 200 and 2000")
        terms = [item.casefold() for item in query.split() if item.strip()]
        documents: list[tuple[str, str]] = []
        if identifier is not None:
            documents.append((identifier, self._text(kind=kind, identifier=identifier)))
        elif kind == "skill":
            documents.extend((item.id, self.read(item.id)) for item in self.list())
        elif kind == "asset":
            documents.extend((item.id, self.read_asset(item.id)) for item in self.assets())
        else:
            raise ValueError("kind must be 'skill' or 'asset'")
        matches: list[dict[str, Any]] = []
        for document_id, text in documents:
            lines = text.splitlines()
            current_heading = ""
            for line_number, line in enumerate(lines, start=1):
                heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
                if heading_match is not None:
                    current_heading = heading_match.group(1)
                lowered = line.casefold()
                score = sum(term in lowered for term in terms)
                if score == 0:
                    continue
                start = max(0, line_number - 3)
                end = min(len(lines), line_number + 2)
                excerpt = "\n".join(lines[start:end]).strip()
                matches.append(
                    {
                        "identifier": document_id,
                        "heading": current_heading,
                        "line": line_number,
                        "score": score,
                        "excerpt": excerpt[:context_chars],
                    }
                )
        matches.sort(key=lambda item: (-item["score"], item["identifier"], item["line"]))
        return {
            "kind": kind,
            "query": query,
            "matches": matches[:limit],
            "truncated": len(matches) > limit,
        }

    def manifest(self) -> list[dict[str, str]]:
        """Return a deterministic workflow-version manifest for event/snapshot provenance."""
        snapshot = self._current()
        return [
            {
                "id": document.id,
                "source": document.source,
                "checksum": document.checksum,
            }
            for document in (*snapshot.documents, *snapshot.assets, *snapshot.dependencies)
        ]

    def publish(self, output_dir: Path) -> Path:
        """Write a content-addressed, reproducible package from captured bytes only."""
        with self._snapshot_lock:
            snapshot = self._current()
            manifest = self.manifest()
            digest = self.package_hash()
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / f"workflow-{digest}.zip"
            if target.exists():
                with zipfile.ZipFile(target) as archive:
                    if json.loads(archive.read("manifest.json")) != manifest:
                        raise ValueError("existing workflow package manifest mismatch")
                    for item in (*snapshot.documents, *snapshot.assets, *snapshot.dependencies):
                        relative = item.path.relative_to(self.root(item.source).resolve())
                        member = f"{item.source}/{relative.as_posix()}"
                        if archive.read(member) != snapshot.contents[item.id]:
                            raise ValueError("existing workflow package content mismatch")
                return target
            with io.BytesIO() as stream:
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:

                    def write(name: str, data: bytes) -> None:
                        entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        entry.external_attr = 0o100644 << 16
                        archive.writestr(entry, data)

                    write("manifest.json", json.dumps(manifest, sort_keys=True).encode())
                    for item in (*snapshot.documents, *snapshot.assets, *snapshot.dependencies):
                        relative = item.path.relative_to(
                            self.root(item.source).resolve()
                        ).as_posix()
                        write(f"{item.source}/{relative}", snapshot.contents[item.id])
                with tempfile.TemporaryDirectory(prefix=".workflow-", dir=output_dir) as temporary:
                    staged = Path(temporary) / "package.zip"
                    with staged.open("xb") as output:
                        output.write(stream.getvalue())
                        output.flush()
                        os.fsync(output.fileno())
                    try:
                        os.link(staged, target)
                    except FileExistsError:
                        return self.publish(output_dir)
            return target

    def package_hash(self) -> str:
        """Hash every exposed workflow document and dependency in this snapshot."""
        encoded = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _text(self, *, kind: str, identifier: str) -> str:
        if kind == "skill":
            return self.read(identifier)
        if kind == "asset":
            return self.read_asset(identifier)
        raise ValueError("kind must be 'skill' or 'asset'")

    @staticmethod
    def _checksum(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _is_install_shadow(path: Path, root: Path) -> bool:
        """Ignore hidden package-manager mirrors such as nested .agents installs."""
        return any(
            part.startswith(".") or part in {"__pycache__", "node_modules"}
            for part in path.relative_to(root).parts
        )

    @staticmethod
    def _title(path: Path, fallback: str) -> str:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return fallback
