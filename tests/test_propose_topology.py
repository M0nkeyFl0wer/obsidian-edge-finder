import tempfile
import unittest
from pathlib import Path

from edge_finder.ontology import compose, load_core
from edge_finder.propose_topology import plan_topology_proposals
from edge_finder.walker import walk_vault


class ProposeTopologyTest(unittest.TestCase):
    def test_plan_topology_proposals_finds_high_confidence_peer_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Climate Hub.md").write_text("# Climate Hub\n", encoding="utf-8")
            (vault / "alpha.md").write_text(
                "---\ntitle: Carbon Policy Brief\nurl: https://example.com/a\ntags: [climate, policy]\n---\n\n"
                "topic_hub:: [[Climate Hub]]\n\n# Carbon Policy Brief\n",
                encoding="utf-8",
            )
            (vault / "beta.md").write_text(
                "---\ntitle: Carbon Policy Update\nurl: https://example.com/b\ntags: [climate, policy]\n---\n\n"
                "topic_hub:: [[Climate Hub]]\n\n# Carbon Policy Update\n",
                encoding="utf-8",
            )

            notes = list(walk_vault(vault))
            proposals = plan_topology_proposals(notes, compose(load_core()))

            self.assertEqual(1, len(proposals))
            self.assertEqual("co_topic", proposals[0].predicate)
            self.assertEqual("high", proposals[0].confidence)
            self.assertIn("shared tags=policy", proposals[0].evidence)


if __name__ == "__main__":
    unittest.main()
