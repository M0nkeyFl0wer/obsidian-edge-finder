import tempfile
import unittest
from pathlib import Path

from edge_finder.walker import walk_vault


class WalkerTest(unittest.TestCase):
    def test_walk_vault_skips_ephemeral_and_backup_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "keep.md").write_text("# Keep\n", encoding="utf-8")
            (vault / "triage-plan.md").write_text("# Skip\n", encoding="utf-8")
            templates = vault / "Templates"
            templates.mkdir()
            (templates / "template.md").write_text("# Template\n", encoding="utf-8")
            backup_dir = vault / "Archive.backup"
            backup_dir.mkdir()
            (backup_dir / "old.md").write_text("# Old\n", encoding="utf-8")

            paths = {note.relpath for note in walk_vault(vault)}

            self.assertEqual({"keep.md"}, paths)


if __name__ == "__main__":
    unittest.main()
