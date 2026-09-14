"""Exercise the portable importer with an isolated, synthetic campaign."""
import argparse
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

spec = importlib.util.spec_from_file_location(
    "portable", Path(__file__).resolve().parents[1] / "standalone/portable.py"
)
portable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portable)


class PortableModuleIngestTests(unittest.TestCase):
    def test_heading_shapes_and_repeated_ingest(self):
        fixtures = [
            ("## Arrival\nFirst scene.\n## Departure\nSecond scene.\n", 1, 2),
            ("Preamble\n### Arrival\nA scene.\n", 1, 1),
            ("# Chapter One\n## Arrival\nText\n# Chapter Two\n## Exit\n", 2, 2),
            ("Plain prose without headings.", 1, 1),
        ]
        original_root = portable._DATA_ROOT
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                portable._DATA_ROOT = root / "data"
                source = root / "case.md"
                for content, chapters, scenes in fixtures:
                    with self.subTest(content=content):
                        source.write_text(content, encoding="utf-8")
                        args = argparse.Namespace(path=str(source), title="Case", campaign="test")
                        result = portable.cmd_module_ingest(args)
                        self.assertEqual((result["chapters"], result["scenes"]), (chapters, scenes))
                        self.assertTrue(portable.cmd_module_ingest(args)["skipped"])
                        stored = root / "data/test/modules/case.md"
                        self.assertEqual(stored.read_text(encoding="utf-8"), content)
        finally:
            portable._DATA_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
