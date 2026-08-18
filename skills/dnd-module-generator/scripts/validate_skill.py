from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/workflow.md",
    "references/system-profile.md",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    missing = sorted(path for path in REQUIRED if not (root / path).is_file())
    if missing:
        fail("missing required files: " + ", ".join(missing))

    skill = (root / "SKILL.md").read_text(encoding="utf-8-sig")
    if not skill.startswith("---\n") or skill.count("---") < 2:
        fail("SKILL.md has no valid frontmatter")
    frontmatter = skill.split("---", 2)[1]
    fields = [
        line.split(":", 1)[0].strip()
        for line in frontmatter.splitlines()
        if line.strip()
    ]
    if fields != ["name", "description"]:
        fail("frontmatter must contain only name and description")
    if not re.search(r"^name:\s*dnd-module-generator\s*$", frontmatter, re.MULTILINE):
        fail("unexpected Skill name")

    corpus = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in sorted(root.rglob("*.md"))
    )
    for marker in ("dnd5e", "module_draft", "rulebook_draft", "content_pack"):
        if marker not in corpus:
            fail(f"missing current contract marker: {marker}")
    for stale in ("coc7e", "Call of Cthulhu", "SagaSmith-module-gen-skills"):
        if stale in corpus:
            fail(f"cross-domain or retired marker found: {stale}")

    metadata = (root / "agents" / "openai.yaml").read_text(encoding="utf-8-sig")
    if "Use $dnd-module-generator" not in metadata:
        fail("agents/openai.yaml has a stale default prompt")
    print("validated D&D Module Generator Skill")


if __name__ == "__main__":
    main()
