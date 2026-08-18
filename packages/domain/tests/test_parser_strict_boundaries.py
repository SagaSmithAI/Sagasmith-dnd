import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "sagasmith_dnd"
PARSER_FILES = (
    PACKAGE_ROOT / "content_import.py",
    PACKAGE_ROOT / "statblocks.py",
)


def test_retired_proximity_and_source_order_inferences_are_not_implemented() -> None:
    retired_functions = {
        "_merge_split_item_continuations",
        "_ordered_class_candidates",
        "_ordered_species_candidates",
        "_repair_split_option_headings",
        "_repair_subclass_table_ocr_identities",
    }
    implemented = {
        node.name
        for path in PARSER_FILES
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert retired_functions.isdisjoint(implemented)


def test_parser_contains_no_book_or_named_entry_repairs() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PARSER_FILES).casefold()
    prohibited = {
        "chapter11spells",
        "dungeon master's guide",
        "ignited illumination",
        "monster manual",
        "mordenkainen",
        "player's handbook",
        "tasha",
        "volo",
        "xanathar",
    }
    assert not {marker for marker in prohibited if marker in combined}


def test_parser_contains_no_retired_fixed_proximity_windows() -> None:
    content_import = (PACKAGE_ROOT / "content_import.py").read_text(encoding="utf-8")
    prohibited = {
        "hard_end - 12",
        "start - 20",
        '"ordered class boundary"',
        '"ordered species traits"',
        '"ordered subrace boundary"',
    }
    assert not {marker for marker in prohibited if marker in content_import}
