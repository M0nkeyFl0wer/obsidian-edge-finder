import tempfile
import unittest
from pathlib import Path

from edge_finder.apply import apply_proposals, parse_proposals


class ApplyTest(unittest.TestCase):
    def test_parse_supports_new_and_legacy_formats(self) -> None:
        text = (
            "## 1. `a.md` → `b.md`\n\n"
            "- [x] Apply: predicate=`co_topic`, confidence=high\n\n"
            "## 2. `c.md` ↔ `d.md`\n\n"
            "- [ ] Apply: edge_type=`same_org`, confidence=medium\n"
        )

        proposals = parse_proposals(text)

        self.assertEqual(2, len(proposals))
        self.assertTrue(proposals[0].directional)
        self.assertFalse(proposals[1].directional)
        self.assertEqual("co_topic", proposals[0].predicate)
        self.assertEqual("same_org", proposals[1].predicate)

    def test_apply_emits_symmetric_typed_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "left.md").write_text("---\ntags: [climate]\n---\n\n# Left\n", encoding="utf-8")
            (vault / "right.md").write_text("---\ntags: [climate]\n---\n\n# Right\n", encoding="utf-8")
            (vault / "proposals.md").write_text(
                "# Proposals\n\n"
                "## 1. `left.md` → `right.md`\n\n"
                "- [x] Apply: predicate=`co_topic`, confidence=high\n"
                "- Evidence: \"shared hub=Climate Hub; shared tags=climate\"\n"
                "- Rationale: direct peer closure\n",
                encoding="utf-8",
            )

            result = apply_proposals(vault, vault / "proposals.md")

            self.assertEqual([], result.errors)
            self.assertEqual(2, result.edges_added)
            self.assertIn("co_topic:: [[right]]", (vault / "left.md").read_text(encoding="utf-8"))
            self.assertIn("co_topic:: [[left]]", (vault / "right.md").read_text(encoding="utf-8"))

    def test_apply_does_not_duplicate_existing_typed_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "left.md").write_text("co_topic:: [[right]]\n\n# Left\n", encoding="utf-8")
            (vault / "right.md").write_text("co_topic:: [[left]]\n\n# Right\n", encoding="utf-8")
            (vault / "proposals.md").write_text(
                "# Proposals\n\n"
                "## 1. `left.md` → `right.md`\n\n"
                "- [x] Apply: predicate=`co_topic`, confidence=high\n"
                "- Evidence: \"shared tags=climate\"\n"
                "- Rationale: direct peer closure\n",
                encoding="utf-8",
            )

            result = apply_proposals(vault, vault / "proposals.md")

            self.assertEqual(2, result.edges_skipped_existing)
            self.assertEqual(0, result.edges_added)


if __name__ == "__main__":
    unittest.main()
