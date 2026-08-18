from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/pack-contract.md",
    "references/system-profiles.md",
    "references/source-authoring.md",
    "references/narrative-patterns.md",
    "references/review-gates.md",
    "references/canonical-example.md",
}

LEGACY_MARKERS = {
    "module_import(",
    "module_write",
    "module_inspect",
    "dnd_module",
    'action="stage"',
    'action="ingest"',
    "portable D&D 5e Module Packs",
    "Core and D&D derive",
}

REQUIRED_SKILL_MARKERS = {
    "module_draft",
    "rulebook_draft",
    "content_pack",
    "sagasmith.content-package",
    "references/pack-contract.md",
    "references/system-profiles.md",
    "references/source-authoring.md",
    "references/review-gates.md",
    "dnd5e",
    "coc7e",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def validate_frontmatter(skill: Path, text: str) -> None:
    if not text.startswith("---\n"):
        fail(f"{skill}: missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        fail(f"{skill}: malformed YAML frontmatter")
    fields: set[str] = set()
    for line in parts[1].splitlines():
        match = re.match(r"^([a-z_]+):", line)
        if match:
            fields.add(match.group(1))
    if fields != {"name", "description"}:
        fail(f"{skill}: frontmatter fields must be exactly name and description")
    if not re.search(r"^name:\s*sagasmith-modulegen\s*$", parts[1], re.MULTILINE):
        fail(f"{skill}: unexpected skill name")


def validate_links(root: Path, path: Path, text: str) -> None:
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = target.split("#", 1)[0]
        if not target or "://" in target or target.startswith("#"):
            continue
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            fail(f"{path}: local link escapes the skill root: {target}")
        if not resolved.exists():
            fail(f"{path}: broken local link: {target}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    missing = sorted(item for item in REQUIRED_FILES if not (root / item).is_file())
    if missing:
        fail("missing required skill files: " + ", ".join(missing))

    mirror = root / ".agents" / "skills" / "sagasmith-modulegen"
    if mirror.is_dir() and any(path.is_file() for path in mirror.rglob("*")):
        fail(f"generated/self-installed skill mirror must not be committed: {mirror}")
    if (root / "skills-lock.json").exists():
        fail("self-referential skills-lock.json must not be committed in the source skill")

    skill = root / "SKILL.md"
    skill_text = skill.read_text(encoding="utf-8-sig")
    validate_frontmatter(skill, skill_text)
    line_count = len(skill_text.splitlines())
    if line_count > 500:
        fail(f"{skill}: {line_count} lines exceeds the 500-line skill budget")
    missing_markers = sorted(REQUIRED_SKILL_MARKERS - set(
        marker for marker in REQUIRED_SKILL_MARKERS if marker in skill_text
    ))
    if missing_markers:
        fail(f"{skill}: missing current contract markers: {', '.join(missing_markers)}")

    markdown = [path for path in root.rglob("*.md") if ".git" not in path.parts]
    for path in markdown:
        text = path.read_text(encoding="utf-8-sig")
        validate_links(root, path, text)
        for marker in LEGACY_MARKERS:
            if marker in text:
                fail(f"{path}: legacy authoring protocol marker found: {marker}")

    metadata = (root / "agents" / "openai.yaml").read_text(encoding="utf-8-sig")
    for marker in (
        'display_name: "',
        'short_description: "',
        'default_prompt: "Use $sagasmith-modulegen',
    ):
        if marker not in metadata:
            fail(f"agents/openai.yaml: missing or stale interface marker: {marker}")
    for stale in ("reviewed D&D Module Packs", "D&D Module Pack from"):
        if stale in metadata:
            fail(f"agents/openai.yaml: stale single-system interface text: {stale}")

    profiles = (root / "references" / "system-profiles.md").read_text(
        encoding="utf-8-sig"
    )
    for marker in (
        "## D&D 5e: dnd5e",
        "## Call of Cthulhu 7e: coc7e",
        "investigator_count",
        "estimated_sessions",
        "starting_level",
        "expected_end_level",
    ):
        if marker not in profiles:
            fail(f"references/system-profiles.md: missing current profile marker: {marker}")

    print(
        f"validated canonical skill: {line_count} SKILL.md lines, "
        f"{len(markdown)} Markdown files"
    )


if __name__ == "__main__":
    main()
