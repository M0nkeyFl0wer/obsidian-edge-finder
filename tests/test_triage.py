import tempfile
import unittest
from pathlib import Path

from edge_finder.triage import apply_plan


class TriageApplyTest(unittest.TestCase):
    def test_apply_plan_emits_typed_topic_hub_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Climate Hub.md").write_text("# Climate Hub\n", encoding="utf-8")
            (vault / "stub.md").write_text(
                "---\nurl: https://example.com\ntags:\n  - climate\n---\n\nStub body\n",
                encoding="utf-8",
            )
            (vault / "triage-plan.md").write_text(
                "# Triage Plan\n\n"
                "## Attachments\n\n"
                "## 1. `stub.md` → `Climate Hub.md`\n\n"
                "- [x] Attach via predicate `topic_hub` from tag `climate` (confidence=high)\n",
                encoding="utf-8",
            )

            result = apply_plan(vault, vault / "triage-plan.md")

            self.assertEqual([], result.errors)
            self.assertEqual(1, result.edges_added)
            self.assertTrue(result.backup_path and result.backup_path.exists())
            body = (vault / "stub.md").read_text(encoding="utf-8")
            self.assertIn("topic_hub:: [[Climate Hub]]", body)


if __name__ == "__main__":
    unittest.main()
